# Panels rebuilt from RAW inputs versus the Codex design files (spec 3.6, 4.2). Every Codex file is a parity reference
# only; the builders read OpenSecrets captures, FEC candidate masters, IBISWorld / CPI / control workbooks, the IEM zip and
# the LobbyView sector-year workbook. Tolerances are the spec's (set from measured residuals with >= 100x headroom).

key_of <- function(d, cols) do.call(paste, c(unname(as.list(d[cols])), sep = "|"))
align <- function(new, ref, cols) {
  kn <- key_of(new, cols); kr <- key_of(ref, cols)
  expect_identical(sort(kn), sort(kr))
  list(new = new[match(kr, kn), , drop = FALSE], ref = ref)
}

test_that("rf_pac_panel reproduces regression_panel.csv (21 rows) from OpenSecrets captures, workbook 07 and CPI", {
  ctx <- test_ctx()
  new <- ctx$pac_panel
  ref <- ref_csv("regression_panel.csv")
  expect_equal(nrow(new), 21L)
  a <- align(new, ref, c("sector", "cycle_end"))
  for (col in c("total_nominal", "individual_nominal", "pac_nominal", "outside_nominal", "democrat_nominal", "republican_nominal"))
    expect_identical(as.numeric(a$new[[col]]), as.numeric(a$ref[[col]]), info = col)
  for (col in c("multiplier_to_2025_dollars", "total_real_2025", "individual_real_2025", "pac_real_2025", "outside_real_2025",
                "log_total", "log_individual", "log_pac", "log_outside")) {
    rel <- max(abs(a$new[[col]] - a$ref[[col]]) / abs(a$ref[[col]]))
    expect_lte(rel, 1e-12, label = paste(col, "max relative deviation", format(rel)))
  }
  for (col in c("cycle_revenue_growth_pct", "prior_cycle_revenue_growth_pct", "G_current", "G_prior"))
    expect_lte(max_abs(a$new[[col]], a$ref[[col]]), 1e-10, label = paste(col, "max abs deviation"))
  expect_identical(as.integer(a$new$Neg_prior), as.integer(a$ref$Neg_prior))
  expect_identical(as.integer(a$new$presidential_cycle), as.integer(a$ref$presidential_cycle))
  # the growth scaling of the bundle: per-industry mean and sd (ddof = 1) over the panel's own cycles
  sc <- attr(new, "scaling")
  expect_setequal(sc$sector, RF$V1_SECTORS)
  expect_equal(sc$cycles, rep(7L, 3))
})

test_that("rf_pac_panel + the frozen formula give pac_selected_design.csv (21 x 10)", {
  ctx <- test_ctx()
  des <- ref_csv("pac_selected_design.csv")
  d <- ctx$pac_panel
  d <- d[order(d$sector, d$cycle_end), ]
  d$sector <- factor(d$sector)
  X <- model.matrix(log_pac ~ sector + factor(cycle_end) + G_current, d)
  expect_identical(dim(X), c(21L, 10L))
  refX <- as.matrix(des[, setdiff(names(des), c("sector", "cycle_end", "y"))])
  expect_identical(colnames(X), colnames(refX))
  expect_identical(as.numeric(X[, colnames(X) != "G_current"]), as.numeric(refX[, colnames(refX) != "G_current"]))
  expect_lte(max_abs(X[, "G_current"], refX[, "G_current"]), 1e-10)
  expect_lte(max_abs(d$log_pac, des$y), 1e-12)
  expect_identical(as.character(d$sector), des$sector)
  expect_identical(as.integer(d$cycle_end), as.integer(des$cycle_end))
})

test_that("rf_money_panel reproduces money_panel.csv (congressional, 42 rows) from 63 captures and the FEC masters", {
  ctx <- test_ctx()
  new <- ctx$money
  ref <- ref_csv("money_panel.csv")
  ref <- ref[ref$account == "congressional", ]
  expect_equal(nrow(new), 42L)
  a <- align(new, ref, c("sector", "cycle_end", "chamber"))
  for (col in c("D_nominal", "R_nominal", "other_nominal", "total_nominal", "recipients", "negative_recipients", "negative_amount",
                "excluded_recipients", "excluded_amount", "Pres"))
    expect_identical(as.numeric(a$new[[col]]), as.numeric(a$ref[[col]]), info = col)
  expect_lte(max_abs(a$new$RShare, a$ref$RShare), 1e-15)
  for (col in c("G_prior", "G_current", "cycle_revenue_growth_pct", "prior_cycle_revenue_growth_pct"))
    expect_lte(max_abs(a$new[[col]], a$ref[[col]]), 1e-10, label = col)
  for (col in c("multiplier_to_2025_dollars", "log_R", "log_D"))
    expect_lte(max(abs(a$new[[col]] - a$ref[[col]]) / abs(a$ref[[col]])), 1e-12, label = col)
  # receipt counts of the Codex build
  rec <- ctx$recipients
  expect_equal(nrow(rec), 16916L)
  expect_equal(sum(rec$presidential_overlap), 64L)
  expect_equal(nrow(ctx$fec_identities), 22L)
  # accounting identities
  expect_true(all(new$D_nominal + new$R_nominal + new$other_nominal == new$total_nominal))
  expect_true(all(new$RShare >= 0 & new$RShare <= 1))
})

test_that("candidate name keys are ASCII, so tolower() equals Python casefold() on every recipient", {
  rec <- test_ctx()$recipients
  expect_true(all(!grepl("[^\\x01-\\x7f]", rec$name_key, perl = TRUE)))
  expect_identical(rec$name_key, tolower(rec$name_key))
})

test_that("OpenSecrets parsers refuse a malformed capture (negative controls)", {
  good <- rf_input_path(test_inputs(), "os_totals:credit_unions")
  tot <- rf_os_totals(good)
  expect_equal(nrow(tot), 18L)   # 1990-2024
  expect_true(all(tot$total_nominal == tot$individual_nominal + tot$pac_nominal + tot$outside_nominal))
  v <- rf_read_json(good)
  # identity broken: total no longer equals individuals + PACs + soft money
  k <- which(vapply(v$tables, function(t) identical(t$rows[[1]][[1]], "Election Cycle"), logical(1)))[1]
  v$tables[[k]]$rows[[2]][[2]] <- "$1"
  p <- file.path(tempdir(), "bad_totals.json"); jsonlite::write_json(v, p, auto_unbox = TRUE)
  expect_error(rf_os_totals(p), "identity")
  v <- rf_read_json(good); v$tables[[k]]$rows[[1]][[2]] <- "Something else"
  p2 <- file.path(tempdir(), "bad_totals2.json"); jsonlite::write_json(v, p2, auto_unbox = TRUE)
  expect_error(rf_os_totals(p2), "header")
})

test_that("rf_iem_window(JanJun) and current control reproduce the control cases of analysis_case_panels.csv", {
  ctx <- test_ctx()
  cp <- ref_csv("analysis_case_panels.csv")
  ref <- unique(cp[cp$case %in% c("control__House__pooled__RShare", "control__Senate__pooled__RShare"),
                   c("cycle_end", "chamber", "P_R", "RMajNow", "days")])
  expect_equal(nrow(ref), 12L)
  a <- align(ctx$iem_jj, ref, c("cycle_end", "chamber"))
  expect_lte(max_abs(a$new$P_R, a$ref$P_R), 1e-14)
  expect_identical(as.integer(a$new$days), as.integer(a$ref$days))
  m <- merge(ctx$iem_jj[, c("cycle_end", "chamber")], ctx$current_control, by.x = c("cycle_end", "chamber"), by.y = c("year", "chamber"))
  m <- m[match(key_of(ref, c("cycle_end", "chamber")), key_of(m, c("cycle_end", "chamber"))), ]
  expect_identical(as.integer(m$RMajNow), as.integer(ref$RMajNow))
  # the window covers every calendar day (181 in an election year Jan 1 - Jun 30, 182 in a leap year)
  expect_true(all(ctx$iem_jj$days == ifelse(ctx$iem_jj$cycle_end %% 4 == 0, 182L, 181L)))
})

test_that("rf_iem_window refuses a window with a missing day (negative control)", {
  daily <- test_ctx()$iem_daily
  dropped <- daily[!(daily$cycle_end == 2014 & daily$chamber == "House" & daily$date == "2014-03-03"), ]
  expect_error(rf_iem_window(dropped, "JanJun"), "calendar day")
})

test_that("rf_seat_calls reproduces the 14 Crystal Ball final calls of the seat cases", {
  ctx <- test_ctx()
  cp <- ref_csv("analysis_case_panels.csv")
  ref <- unique(cp[cp$case %in% c("seats__House__pooled__RShare", "seats__Senate__pooled__RShare"),
                   c("cycle_end", "chamber", "pred_rep_net_change", "RMajNow")])
  expect_equal(nrow(ref), 14L)
  a <- align(ctx$seat_calls[ctx$seat_calls$cycle_end %in% seq(2012, 2024, 2), ], ref, c("cycle_end", "chamber"))
  expect_identical(as.numeric(a$new$pred_rep_net_change), as.numeric(a$ref$pred_rep_net_change))
})

test_that("rf_share_panel reproduces the four pooled RShare cases of analysis_case_panels.csv", {
  ctx <- test_ctx()
  cp <- ref_csv("analysis_case_panels.csv")
  spec <- list(control__House = "control_House", control__Senate = "control_Senate", seats__House = "seats_House", seats__Senate = "seats_Senate")
  rows <- c(control__House = 18L, control__Senate = 18L, seats__House = 21L, seats__Senate = 21L)
  for (nm in names(spec)) {
    ref <- cp[cp$case == paste0(nm, "__pooled__RShare"), ]
    new <- ctx$share[[spec[[nm]]]]
    expect_equal(nrow(new), rows[[nm]], info = nm)
    a <- align(new, ref, c("sector", "cycle_end"))
    expect_lte(max_abs(a$new$RShare, a$ref$RShare), 1e-15, label = paste(nm, "RShare"))
    expect_lte(max_abs(a$new$Forecast, a$ref$Forecast), 1e-14, label = paste(nm, "Forecast"))
    expect_identical(as.integer(a$new$RMajNow), as.integer(a$ref$RMajNow), info = nm)
    expect_identical(as.integer(a$new$Pres), as.integer(a$ref$Pres), info = nm)
    expect_lte(max_abs(a$new$G_prior, a$ref$G_prior), 1e-10)
    expect_lte(max_abs(a$new$G_current, a$ref$G_current), 1e-10)
    expect_lte(max_abs(a$new$log_R, a$ref$log_R), 1e-11)
    expect_lte(max_abs(a$new$log_D, a$ref$log_D), 1e-11)
  }
})

test_that("the 48 analysis cases have the Codex keys, sizes and outcome columns", {
  ctx <- test_ctx()
  cp <- ref_csv("analysis_case_panels.csv")
  expect_setequal(unique(ctx$cases$case), unique(cp$case))
  expect_equal(length(unique(ctx$cases$case)), 48L)
  n_new <- table(ctx$cases$case); n_ref <- table(cp$case)
  expect_identical(as.integer(n_new[names(n_ref)]), as.integer(n_ref))
  # row order inside a case is (cycle_end, sector), as analyze.py sorts it
  for (cs in c("control__House__pooled__RShare", "seats__Senate__commercial_banks__log_D")) {
    d <- ctx$cases[ctx$cases$case == cs, ]
    expect_false(is.unsorted(order(d$cycle_end, d$sector)) && !identical(order(d$cycle_end, d$sector), seq_len(nrow(d))))
  }
})

test_that("rf_lobby_levels and rf_lobby_diffs reproduce level_sample.csv (77) and year_effects__all71.csv (71)", {
  ctx <- test_ctx()
  lv <- ref_csv("level_sample.csv")
  new <- ctx$lobby$levels
  expect_equal(nrow(new), 77L)
  expect_identical(as.list(table(new$study_sector)),
                   list(auto_manufacturing = 2L, commercial_banks = 16L, credit_unions = 16L, life_insurance = 13L,
                        property_casualty = 15L, telecom = 15L))
  a <- align(new, lv, c("study_sector", "year"))
  expect_lte(max_abs(a$new$y, a$ref$y), 1e-11)
  expect_lte(max_abs(a$new$g, a$ref$g), 1e-12)
  des <- ref_csv("year_effects__all71.csv")
  df <- ctx$lobby$diffs
  expect_equal(nrow(df), 71L)
  b <- align(df, data.frame(study_sector = des$sector, year = des$year, dy = des$y, dg = des$dg, check.names = FALSE), c("study_sector", "year"))
  expect_lte(max_abs(b$new$dy, b$ref$dy), 1e-11)
  expect_lte(max_abs(b$new$dg, b$ref$dg), 1e-12)
  # design columns (intercept + year dummies) are exact
  X <- ctx$lobby$design_matrix
  refX <- as.matrix(des[, setdiff(names(des), c("sector", "year", "y"))])
  refX <- refX[match(key_of(b$ref, c("study_sector", "year")), key_of(data.frame(study_sector = des$sector, year = des$year), c("study_sector", "year"))), , drop = FALSE]
  Xn <- X[match(key_of(b$ref, c("study_sector", "year")), key_of(df, c("study_sector", "year"))), , drop = FALSE]
  expect_identical(colnames(Xn), colnames(refX))
  expect_identical(as.numeric(Xn[, colnames(Xn) != "dg"]), as.numeric(refX[, colnames(refX) != "dg"]))
})

test_that("telecom growth is the revenue-weighted wireless + cable component forecast (proven to 3.6e-15)", {
  ctx <- test_ctx()
  lv <- ref_csv("level_sample.csv")
  tel <- ctx$lobby$levels[ctx$lobby$levels$study_sector == "telecom", ]
  ref <- lv[lv$study_sector == "telecom", ]
  expect_equal(nrow(tel), 15L)
  m <- merge(tel, ref, by = "year", suffixes = c("", ".ref"))
  expect_lte(max_abs(m$g, m$g.ref), 3.6e-15 * 100)
})

test_that("telecom scored panel (owner ruling 2026-10-08): OpenSecrets B09 totals only, actual growth = the revenue-weighted wireless + cable series with 2024 from the same editions' estimates", {
  inp <- test_inputs()
  sc <- rf_scored_panel(rf_v2_registry(RF_TEST_REG, inp), inp)
  expect_identical(sc$ids, "telecom"); p <- sc$panel
  expect_identical(p$cycle_end, seq(2012L, 2024L, 2L)); expect_true(all(p$sector == "telecom")); expect_true(all(p$opensecrets_code == "B09"))
  expect_true(all(p$source_url == "https://www.opensecrets.org/industries/totals?ind=B09&cycle=2026"))
  expect_equal(sum(attr(p, "checks")$failures), 0L)
  # the B09 page's own numbers (Totals by Election Cycle), nominal dollars; B08 (Telephone Utilities) is not in them
  expect_identical(p$total_nominal, c(19672059, 15311883, 25762436, 19537375, 34133861, 21466393, 28075186))
  expect_identical(p$individual_nominal, c(8535938, 5196198, 15434800, 8134059, 18981239, 9831367, 15124728))
  expect_identical(p$pac_nominal, c(8248885, 9237509, 9518480, 9726200, 9776300, 8502850, 8636700))
  expect_identical(p$outside_nominal, p$total_nominal - p$individual_nominal - p$pac_nominal)
  # growth: independent re-computation from workbook 07's two components + the 2024 estimate rows, as the growth of the SUMMED revenue
  w7 <- rf_read_xlsx(rf_input_path(inp, "ibis_actual"), "data")
  cur <- utils::read.csv(rf_input_path(inp, "ibis_actual_current:telecom"), stringsAsFactors = FALSE, colClasses = c(report_vintage = "character"))
  lvl <- function(sec) { q <- w7[w7$sector == sec, ]; x <- stats::setNames(q$revenue_million_report_price_basis, q$year); r <- cur[cur$sector == sec, ]; x[as.character(r$year)] <- r$revenue_million_report_price_basis; x }
  wl <- lvl("telecom_wireless"); cb <- lvl("telecom_cable")
  expect_identical(names(wl), as.character(2006:2024)); expect_identical(names(cb), as.character(2006:2024))
  expect_identical(unname(cur$report_vintage), c("2024-10", "2024-12"))                                  # wireless 2024-10, cable 2024-12: each component its own single vintage
  tot <- wl + cb
  cyc <- function(e) tot[[as.character(e - 1)]] + tot[[as.character(e)]]
  rate <- function(e) 100 * (cyc(e) / cyc(e - 2) - 1)
  expect_equal(p$cycle_revenue_growth_pct, vapply(p$cycle_end, rate, 0), tolerance = 1e-12)
  expect_equal(p$prior_cycle_revenue_growth_pct, vapply(p$cycle_end - 2L, rate, 0), tolerance = 1e-12)
  # = the average of the two components' cycle growth weighted by each one's baseline-cycle revenue
  ccyc <- function(x, e) x[[as.character(e - 1)]] + x[[as.character(e)]]
  wavg <- function(e) { w <- c(ccyc(wl, e - 2), ccyc(cb, e - 2)); g <- c(ccyc(wl, e) / ccyc(wl, e - 2) - 1, ccyc(cb, e) / ccyc(cb, e - 2) - 1); 100 * sum(w * g) / sum(w) }
  expect_equal(p$cycle_revenue_growth_pct, vapply(p$cycle_end, wavg, 0), tolerance = 1e-12)
  expect_true(any(attr(p, "estimates")$sector == "telecom") && all(attr(p, "estimates")$year == 2024L))
  # the same panel construction as the pooled industries: deflated by cpi[2025] / mean(cpi[t-1], cpi[t]), standardized within the industry
  expect_equal(p$pac_real_2025, p$pac_nominal * p$multiplier_to_2025_dollars, tolerance = 1e-12)
  expect_equal(mean(p$G_current), 0, tolerance = 1e-12); expect_equal(stats::sd(p$G_current), 1, tolerance = 1e-12)
  # components on different price bases cannot be weighted
  g <- data.frame(sector = rep(c("a", "b"), each = 2), year = rep(2020:2021, 2), revenue_million_constant_2026_usd = NA_real_, revenue_million_report_price_basis = c(10, 11, 20, 22),
                  revenue_price_basis = rep(c("constant 2024 USD", "current USD"), each = 2), report_vintage = "v", source_url = "u", ibisworld_code = "1", status = "observed", stringsAsFactors = FALSE)
  expect_error(rf_combine_components(g, list(ab = c("a", "b"))), "different price bases")
  g$revenue_price_basis <- "constant 2024 USD"
  expect_equal(rf_combine_components(g, list(ab = c("a", "b")))$revenue_million_report_price_basis, c(30, 33))
})

test_that("rf_market_window gives the Jan-Jun 2026 expected-control inputs, per market and equal-weighted", {
  ctx <- test_ctx()
  mw <- ctx$market_window
  expect_equal(nrow(mw$markets), 4L)
  expect_true(all(mw$markets$days == 181L))
  get <- function(ch, mk) mw$markets$p_rep[mw$markets$chamber == ch & mw$markets$market == mk]
  expect_lte(abs(get("House", "kalshi") - 0.20317374), 1e-8)
  expect_lte(abs(get("House", "polymarket") - 0.17752327), 1e-8)
  expect_lte(abs(get("Senate", "kalshi") - 0.57076995), 1e-8)
  expect_lte(abs(get("Senate", "polymarket") - 0.55713597), 1e-8)
  expect_lte(abs(mw$control$p_rep[mw$control$chamber == "House"] - 0.1903485028), 1e-8)
  expect_lte(abs(mw$control$p_rep[mw$control$chamber == "Senate"] - 0.5639529613), 1e-8)
  expect_true(all(nchar(mw$markets$sha256) == 64L))
  # method check against the workbook-02 year-to-date values (2026-01-01..2026-09-24)
  ytd <- ctx$market_ytd
  expect_lte(abs(ytd$p_rep[ytd$chamber == "House" & ytd$market == "kalshi"] - 0.188485), 1e-6)
  expect_lte(abs(ytd$p_rep[ytd$chamber == "Senate" & ytd$market == "polymarket"] - 0.541083), 1e-6)
})

test_that("rf_panel_checks flags each defect of a panel (negative controls) and passes the real panels", {
  ctx <- test_ctx()
  for (nm in c("pac_panel", "money")) {
    kind <- if (nm == "pac_panel") "pac" else "money"
    ck <- rf_panel_checks(ctx[[nm]], kind)
    expect_true(all(ck$failures == 0L), info = paste(nm, paste(ck$name[ck$failures > 0], collapse = ", ")))
  }
  for (nm in names(ctx$share)) expect_true(all(rf_panel_checks(ctx$share[[nm]], "share")$failures == 0L), info = nm)
  expect_true(all(rf_panel_checks(ctx$lobby$diffs, "lobby")$failures == 0L))
  bad <- ctx$pac_panel
  bad <- rbind(bad, bad[1, ])
  expect_gt(sum(rf_panel_checks(bad, "pac")$failures[rf_panel_checks(bad, "pac")$name == "unique keys"]), 0L)
  bad <- ctx$pac_panel; bad$pac_nominal[2] <- 0; bad$pac_real_2025[2] <- 0
  expect_gt(rf_panel_checks(bad, "pac")$failures[rf_panel_checks(bad, "pac")$name == "positive money for logs"], 0L)
  bad <- ctx$share$control_House; bad$RShare[3] <- 1.2
  expect_gt(rf_panel_checks(bad, "share")$failures[rf_panel_checks(bad, "share")$name == "0 <= RShare <= 1"], 0L)
  bad <- ctx$share$control_House; bad$G_prior[4] <- NA
  expect_gt(rf_panel_checks(bad, "share")$failures[rf_panel_checks(bad, "share")$name == "no NA in model columns"], 0L)
  bad <- ctx$money; bad$other_nominal[5] <- bad$other_nominal[5] + 1
  expect_gt(rf_panel_checks(bad, "money")$failures[rf_panel_checks(bad, "money")$name == "D+R+other = total"], 0L)
  bad <- ctx$pac_panel; bad$total_nominal[6] <- bad$total_nominal[6] + 7
  expect_gt(rf_panel_checks(bad, "pac")$failures[rf_panel_checks(bad, "pac")$name == "total = individual + PAC + outside"], 0L)
})
