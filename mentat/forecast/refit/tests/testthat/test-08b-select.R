# Rule auto-pick (Paul's rulings 2026-09-30): for each term run the no-leakage backtests for R0 pooled / R1 own / R2 DL blend in BOTH
# designs (leave-one-election-out and chronological; lobbying has its one rolling-origin design). A non-pooled rule must beat the
# POOLED rule by at least RF$RULE_MIN_GAIN (5%) in every design; the lower MAE in both designs wins between R1 and R2, R2 when the
# designs disagree, and the pooled slope R0 when nothing clears the bar. Library code: not wired into the v1 parity bundle.

test_that("the owner's default margin is 5%", {
  expect_equal(RF$RULE_MIN_GAIN, 0.05)
})

test_that("PAC: own slopes (R1) are chosen, with the SPEC section 8 comparison table recorded", {
  sp <- test_specs()
  sel <- rf_select_rule(sp$pac, path = "prevchg")
  expect_identical(sel$choice, "R1")
  expect_identical(sel$reported_rule, "R1")
  expect_false(sel$resolved_to_pooled)
  expect_identical(sort(sel$designs), c("chrono", "loeo"))
  tab <- sel$table
  get <- function(ds, rule) tab$MAE[tab$design == ds & tab$rule == rule]
  expect_equal(round(c(get("loeo", "R0"), get("loeo", "R1"), get("loeo", "R2"), get("loeo", "naive_prev")), 3), c(21.918, 10.765, 14.349, 12.712))
  expect_equal(round(c(get("chrono", "R0"), get("chrono", "R1"), get("chrono", "R2"), get("chrono", "naive_prev")), 3), c(32.481, 14.314, 21.585, 12.337))
  expect_identical(sel$qualifying, c("R1", "R2"))            # both clear 5% over pooled; R1 has the lower MAE in both designs
  expect_match(sel$reason, "R1 has the lowest MAE in every design")
  expect_true(all(c("term", "design", "rule", "n", "folds", "MAE", "RMSE", "units") %in% names(tab)))
})

test_that("control House, control Senate and lobbying resolve to the pooled slope R0 on today's data", {
  sp <- test_specs()
  for (k in c("control_House.selected", "control_Senate.selected")) {
    sel <- rf_select_rule(sp[[k]], path = "prevchg")
    expect_identical(sel$choice, "R0", info = k)
    expect_identical(sel$reported_rule, "R0", info = k)
    expect_identical(sel$qualifying, character(), info = k)
    expect_match(sel$reason, "no rule beats the pooled slope", info = k)
  }
  lob <- rf_select_rule(sp$lobby, path = "calc")
  expect_identical(lob$designs, "rolling")
  expect_identical(lob$choice, "R0")
  expect_identical(lob$reported_rule, "R0")
  # the margins behind it: lobbying R1 is only 0.8% better than pooled, Senate R1 (calculator path of the old anchor) 1.2%
  g <- function(sel, rule) sel$table$MAE[sel$table$rule == rule]
  expect_equal(g(lob, "R0"), 4.304, tolerance = 1e-3); expect_equal(g(lob, "R1"), 4.271, tolerance = 1e-3)
})

test_that("select_rules returns the four terms of the ruling with the ruled paths", {
  sel <- rf_select_rules(test_specs())
  expect_identical(names(sel), c("pac", "control_House", "control_Senate", "lobby"))
  expect_identical(vapply(sel, function(s) s$reported_rule, ""), c(pac = "R1", control_House = "R0", control_Senate = "R0", lobby = "R0"))
  expect_identical(vapply(sel, function(s) s$path, ""), c(pac = "prevchg", control_House = "prevchg", control_Senate = "prevchg", lobby = "calc"))
  tab <- rf_selection_table(sel)
  expect_true(all(c("choice", "reported_rule", "selection_term") %in% names(tab)))
  expect_equal(nrow(tab), 4 * 2 * 4 - 4)    # three terms x 2 designs x 4 rules + lobbying 1 design x 4 rules
})

test_that("without a margin (min_gain = 0) the literal 'strictly lower than pooled in every design' rule picks own slopes for Senate and lobbying", {
  sp <- test_specs()
  sen <- rf_select_rule(sp$control_Senate.selected, path = "calc", min_gain = 0)
  expect_identical(sen$choice, "R1")
  lob <- rf_select_rule(sp$lobby, path = "calc", min_gain = 0)
  expect_identical(lob$choice, "R1")
  # House (old same-phase path): R1 loses chronologically, the blend beats pooled in both designs, and with tau2 = 0 it IS the pooled slope
  hou <- rf_select_rule(sp$control_House.selected, path = "calc", min_gain = 0)
  expect_identical(hou$choice, "R2")
  expect_true(hou$resolved_to_pooled)
  expect_identical(hou$reported_rule, "R0")
})

test_that("rf_choose_rule: margins, ties, disagreement and single designs (unit)", {
  mk <- function(loeo, chrono = NULL) {
    d <- data.frame(design = "loeo", rule = c("R0", "R1", "R2", "naive_prev"), MAE = c(loeo, 9), stringsAsFactors = FALSE)
    if (!is.null(chrono)) d <- rbind(d, data.frame(design = "chrono", rule = c("R0", "R1", "R2", "naive_prev"), MAE = c(chrono, 9), stringsAsFactors = FALSE))
    d
  }
  two <- c("loeo", "chrono")
  # R1 10% better than pooled in both designs, R2 equal to pooled -> R1
  expect_identical(rf_choose_rule(mk(c(10, 9, 10), c(20, 18, 20)), two)$choice, "R1")
  # exactly 5% better counts (at least 5%); 4.9% does not
  expect_identical(rf_choose_rule(mk(c(10, 9.5, 10), c(20, 19, 20)), two)$choice, "R1")
  expect_identical(rf_choose_rule(mk(c(10, 9.51, 10), c(20, 19, 20)), two)$choice, "R0")
  # clears 5% in one design only -> pooled
  expect_identical(rf_choose_rule(mk(c(10, 9, 10), c(20, 20, 20)), two)$choice, "R0")
  # R1 and R2 both clear it; R1 lower in one design, R2 in the other -> the blend R2
  r <- rf_choose_rule(mk(c(10, 8, 8.5), c(20, 18.5, 17)), two)
  expect_identical(r$choice, "R2"); expect_match(r$reason, "disagree")
  # both clear it and R2 is lower in both -> R2
  expect_identical(rf_choose_rule(mk(c(10, 8, 7), c(20, 16, 15)), two)$choice, "R2")
  # a single design decides alone and must clear the margin itself
  expect_identical(rf_choose_rule(mk(c(10, 9, 10)), "loeo")$choice, "R1")
  expect_identical(rf_choose_rule(mk(c(10, 9.9, 10)), "loeo")$choice, "R0")
  # worse than pooled never wins, whatever the margin
  expect_identical(rf_choose_rule(mk(c(10, 12, 11), c(20, 25, 22)), two, min_gain = 0)$choice, "R0")
})

test_that("the selection table and choice are deterministic (negative control)", {
  sp <- test_specs()
  a <- rf_select_rule(sp$pac, path = "prevchg")
  b <- rf_select_rule(sp$pac, path = "prevchg")
  expect_identical(a$table, b$table)
  expect_identical(a$choice, b$choice)
})

# ---- anchor auto-pick (owner ruling 2026-09-30) -------------------------------------------------------------------------------------

test_that("rf_choose_anchor: same-phase only when it beats previous-cycle by the margin in EVERY design", {
  ch <- function(prev, same, ...) rf_choose_anchor(c(loeo = prev[1], chrono = prev[2]), c(loeo = same[1], chrono = same[2]), ...)
  expect_identical(ch(c(1.544, 5.082), c(1.053, 5.958))$anchor, "previous_cycle")      # banks House today: wins loeo (-32%), loses chrono
  expect_identical(ch(c(1.544, 5.082), c(1.053, 4.500))$anchor, "same_phase")          # wins both (-32%, -11%)
  expect_identical(ch(c(10, 10), c(9.5, 9.5))$anchor, "same_phase")                    # exactly 5% better: clears (<=)
  expect_identical(ch(c(10, 10), c(9.51, 9.0))$anchor, "previous_cycle")               # 4.9% in one design: does not clear
  expect_identical(ch(c(10, 10), c(10, 10))$anchor, "previous_cycle")                  # a tie keeps the default
  expect_identical(ch(c(10, 10), c(9, 9), min_gain = 0)$anchor, "same_phase")          # min_gain 0: strictly lower in every design
  expect_identical(ch(c(10, 10), c(9, NA))$anchor, "previous_cycle")                   # a design without a same-phase backtest cannot prove a win
  r <- ch(c(1.544, 5.082), c(1.053, 5.958))
  expect_equal(unname(r$gain), c(0.3177, -0.1724), tolerance = 1e-3); expect_identical(unname(r$wins), c(TRUE, FALSE))
  expect_match(r$reason, "previous_cycle kept"); expect_match(r$reason, "loeo +31.8%", fixed = TRUE)
  expect_match(ch(c(10, 10), c(9, 9))$reason, "same_phase beats previous_cycle by 5%")
})

test_that("rf_select_anchors picks per industry and chamber from the backtest cells, under the chosen slope rule", {
  v <- test_v2()
  a <- v$anchors
  expect_setequal(names(a$entries), c("control_House", "control_Senate"))
  expect_setequal(names(a$entries$control_House), c("auto_light_duty_manufacturing", "commercial_banks", "credit_unions"))
  tb <- a$table
  expect_equal(nrow(tb), 2L * 3L * 2L)                                                 # chambers x industries x designs
  expect_true(all(tb$rule == "R0"))                                                    # the rule the auto-pick chose for both chambers today
  row <- function(term, ind, ds) tb[tb$term == term & tb$industry == ind & tb$design == ds, ]
  expect_equal(row("control_House", "commercial_banks", "loeo")$MAE_same_phase, 1.0535, tolerance = 1e-3)
  expect_equal(row("control_House", "commercial_banks", "loeo")$MAE_previous_cycle, 1.5439, tolerance = 1e-3)
  expect_equal(row("control_House", "credit_unions", "chrono")$MAE_same_phase, 4.573, tolerance = 1e-3)   # same-phase wins chrono for credit unions,
  expect_equal(row("control_House", "credit_unions", "loeo")$MAE_same_phase, 1.522, tolerance = 1e-3)      # but loses leave-one-election-out: stays previous
  expect_true(all(tb$choice == "previous_cycle"))
  # a different margin changes the outcome only where both designs clear it: none do today even at min_gain = 0
  a0 <- rf_select_anchors(v$backtests, v$selection, lapply(v$shares, function(s) levels(factor(s$spec$d$sector))), min_gain = 0)
  expect_true(all(a0$table$choice[a0$table$term == "control_House"] == "previous_cycle"))      # the House cells; the Senate (Pres term, owner-mandated) may flip a cell at a zero margin
  # the per-industry calculator-path MAE follows the chosen anchor
  m <- rf_chosen_anchor_mae(v$backtests, a, "control_House", "loeo", "R0")
  expect_equal(m[["commercial_banks"]], 1.5439, tolerance = 1e-3)
  forced <- a; forced$entries$control_House$commercial_banks$anchor <- "same_phase"
  expect_equal(rf_chosen_anchor_mae(v$backtests, forced, "control_House", "loeo", "R0")[["commercial_banks"]], 1.0535, tolerance = 1e-3)
})
