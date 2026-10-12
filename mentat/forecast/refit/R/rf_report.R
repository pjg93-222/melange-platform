# summary(lm()) printing: every regression is shown as the real R output (Call / Residuals / Coefficients with stars / RSE /
# R-squared / F), each followed by the labelled robustness block, as SP/regressions/regressions_output.txt.

rf_summary_text <- function(m) {
  op <- options(width = 120, digits = 7, show.signif.stars = TRUE)
  on.exit(options(op))
  utils::capture.output(print(summary(m)))
}

RF_REPORT_TITLES <- c(
  pac = "MODEL 1  PAC growth model (v1 pac_model; forward-selected: log_pac ~ sector + factor(cycle_end) + G_current)",
  pac_complete = "MODEL 1b  PAC original complete growth equation (evidence only; not in the bundle)",
  control_House = "MODEL control_House  (v1 share_models.control_House; frozen mask 11: Forecast + G_prior + Pres)",
  control_Senate = "MODEL control_Senate  (v1 share_models.control_Senate; frozen mask 3: Forecast + G_prior)",
  seats_House = "MODEL seats_House  (v1 share_models.seats_House; evidence only, original full model)",
  seats_Senate = "MODEL seats_Senate  (v1 share_models.seats_Senate; evidence only, original full model)",
  control_House_full = "ORIGINAL full Study-2 control equation, House (mask 15)",
  control_Senate_full = "ORIGINAL full Study-2 control equation, Senate (mask 15)",
  lobby = "MODEL lobby  (v1 lobby_model: dy ~ dg + factor(year), 71 annual pairs)")

# The full regressions.txt of a run: banner, summary(), coefficient-match line against the v1 bundle where one exists, robustness block.
rf_regressions_report <- function(ctx, b_random = RF$B_RANDOM, v1 = NULL) {
  ms <- ctx$models$models
  banner <- function(t) c("", strrep("=", 110), t, strrep("=", 110))
  txt <- c(sprintf("Giving Forecast regressions re-estimated in R (%s) by the refit pipeline %s", R.version.string, RF$VERSION),
           sprintf("Packages: sandwich %s + clubSandwich %s (vendored library, no network install)", utils::packageDescription("sandwich")$Version,
                   utils::packageDescription("clubSandwich")$Version),
           sprintf("Wild bootstrap: restricted (null imposed), CR1-studentized, symmetric two-sided; full enumeration when 6^G <= %d, else %d draws of the numpy-compatible stream.",
                   RF$MAX_ENUM, b_random))
  order <- c("pac", "pac_complete", "control_House", "control_Senate", "seats_House", "seats_Senate", "control_House_full", "control_Senate_full", "lobby")
  for (id in order) {
    m <- ctx$fits[[id]]; d <- ctx$fits_data[[id]]
    focal <- ms[[id]]$focal
    clusters <- unlist(ms[[id]]$clusters)
    txt <- c(txt, banner(RF_REPORT_TITLES[[id]]), rf_summary_text(m))
    if (!is.null(v1)) {
      line <- rf_match_line(id, m, focal, v1)
      if (!is.null(line)) txt <- c(txt, line)
    }
    blk <- rf_robustness_block(id, m, d, focal, clusters, fold_vars = if (id == "lobby") c("year", "study_sector") else if (ms[[id]]$kind == "pac") c("cycle_end", "sector") else c("cycle_end", "sector"),
                               b_random = b_random)
    txt <- c(txt, "", blk$text)
  }
  for (id in c("control_House_industry", "control_Senate_industry", "seats_House_industry", "seats_Senate_industry")) {
    txt <- c(txt, banner(paste("INDUSTRY-SLOPE VARIANT", id, "(v1 share_models; original full model + one forecast slope per industry)")), rf_summary_text(ctx$fits[[id]]))
  }
  txt
}

# COEFFICIENT MATCH line of regressions.R: how far the lm() estimate is from the v1 bundle value.
rf_match_line <- function(id, m, focal, v1) {
  bm <- if (id == "pac") NULL else v1$share_models[[id]]
  if (id == "pac") {
    b <- v1$pac_model; s <- summary(m)$coefficients["G_current", ]
    return(sprintf("COEFFICIENT MATCH vs v1 pac_model: beta diff %.3e ; SE diff %.3e ; ordinary_p diff %.3e ; critical_95 diff %.3e",
                   s[1] - b$beta, s[2] - b$ordinary_se, s[4] - b$ordinary_p, stats::qt(.975, stats::df.residual(m)) - b$critical_95))
  }
  if (id == "lobby") {
    b <- v1$lobby_model; s <- summary(m)$coefficients["dg", ]
    return(sprintf("COEFFICIENT MATCH vs v1 lobby_model: beta diff %.3e ; SE diff %.3e ; ordinary_p diff %.3e", s[1] - b$beta_million, s[2] - b$ordinary_se_million, s[4] - b$ordinary_p))
  }
  if (is.null(bm)) return(NULL)
  terms <- rf_bundle_terms(m, focal)
  bt <- unlist(bm$terms); bb <- unlist(bm$beta)
  new <- stats::setNames(stats::coef(m), terms)
  sprintf("COEFFICIENT MATCH vs v1 %s: %d terms, max |lm - bundle| beta = %.3e", id, length(bt), max(abs(new[bt] - bb)))
}
