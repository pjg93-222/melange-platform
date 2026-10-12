# Shared synthetic industry x cycle panel of the phase tests (planted level process; see test-20-phase.R).
synth_panel <- function(phi = 0.5, beta = -0.2, drift = 0.1, shock = 0, noise = 1e-7) {
  cyc <- seq(2012L, 2024L, by = 2L)
  secs <- c("alpha", "beta", "gamma")
  set.seed(11)
  d <- do.call(rbind, lapply(seq_along(secs), function(i) {
    g <- switch(i, c(0.3, -0.5, 1.1, 0.2, -0.9, 0.7, -0.1), c(-0.1, 0.7, -0.9, 0.2, 1.1, -0.5, 0.3), c(-0.4, 0.9, 0.1, -1.2, 0.6, 0.3, 0.5))      # different shapes: growth is not collinear with the cycle effects
    data.frame(sector = secs[i], cycle_end = cyc, G_current = g, presidential_cycle = as.integer(cyc %% 4 == 0), cycle_revenue_growth_pct = 5 * g + 3 + i,
               prior_cycle_revenue_growth_pct = 5 * c(0, g[-length(g)]) + 3 + i, stringsAsFactors = FALSE)
  }))
  d$y <- NA_real_
  for (s in secs) {
    ix <- which(d$sector == s)
    y <- 10 + match(s, secs)
    for (k in seq_along(ix)) {
      if (k > 1L) y <- y + drift + beta * (d$G_current[ix[k]] - d$G_current[ix[k - 1L]]) + phi * (d$presidential_cycle[ix[k]] - d$presidential_cycle[ix[k - 1L]])
      d$y[ix[k]] <- y + shock * (d$cycle_end[ix[k]] == 2022L) + noise * stats::rnorm(1)
    }
  }
  d$sector <- factor(d$sector)
  d
}
