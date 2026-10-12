script_argument <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
stopifnot(length(script_argument) == 1L)
script_path <- normalizePath(
  sub("^--file=", "", script_argument[[1L]]),
  winslash = "/",
  mustWork = TRUE
)
project_root <- normalizePath(
  file.path(dirname(script_path), ".."),
  winslash = "/",
  mustWork = TRUE
)

suppressPackageStartupMessages(library(data.table))
source(
  file.path(project_root, "R", "identity_components.R"),
  local = .GlobalEnv
)

nonblank_equal_reference <- function(left, right) {
  nzchar(left) && nzchar(right) && identical(left, right)
}

# Frozen reference implementation from the report exporter before the
# indexed refactor. This test proves exact component-number parity, not merely
# equal component counts.
assign_identity_components_reference <- function(group) {
  group <- data.table::copy(group)
  count <- nrow(group)
  if (!count) {
    group[, identity_component := integer()]
    return(group)
  }
  parent <- seq_len(count)
  root <- function(index) {
    while (parent[[index]] != index) {
      parent[[index]] <<- parent[[parent[[index]]]]
      index <- parent[[index]]
    }
    index
  }
  unite <- function(left, right) {
    left_root <- root(left)
    right_root <- root(right)
    if (left_root != right_root) {
      parent[[right_root]] <<- left_root
    }
  }

  if (any(group$known_executive) || any(group$verified_employee)) {
    if (count > 1L) {
      for (index in 2L:count) {
        unite(1L, index)
      }
    }
  } else if (count > 1L) {
    for (left in seq_len(count - 1L)) {
      for (right in seq.int(left + 1L, count)) {
        same_exact <- nonblank_equal_reference(
          group$exact_name_key[[left]],
          group$exact_name_key[[right]]
        )
        same_zip <- nonblank_equal_reference(
          substr(group$zipcode[[left]], 1L, 5L),
          substr(group$zipcode[[right]], 1L, 5L)
        )
        same_location <- nonblank_equal_reference(
          paste(group$city[[left]], group$state[[left]], sep = "|"),
          paste(group$city[[right]], group$state[[right]], sep = "|")
        ) && nzchar(group$city[[left]]) && nzchar(group$state[[left]])
        same_occupation <- nonblank_equal_reference(
          group$occupation_key[[left]],
          group$occupation_key[[right]]
        )
        same_employer <- nonblank_equal_reference(
          group$employer_key[[left]],
          group$employer_key[[right]]
        )
        merge_exact <- same_exact && (
          same_zip || same_location || same_occupation
        )
        merge_variant <- !same_exact && (
          same_zip || same_location || (same_occupation && same_employer)
        )
        if (merge_exact || merge_variant) {
          unite(left, right)
        }
      }
    }
  }

  roots <- vapply(seq_len(count), root, integer(1L))
  root_order <- unique(roots[order(
    group$exact_name_key,
    group$state,
    group$zipcode,
    group$sub_id
  )])
  group[, identity_component := match(roots, root_order)]
  group
}

make_group <- function(
  exact_name_key,
  zipcode = rep("", length(exact_name_key)),
  city = rep("", length(exact_name_key)),
  state = rep("", length(exact_name_key)),
  occupation_key = rep("", length(exact_name_key)),
  employer_key = rep("", length(exact_name_key)),
  known_executive = rep(FALSE, length(exact_name_key)),
  verified_employee = rep(FALSE, length(exact_name_key))
) {
  data.table(
    sub_id = sprintf("sub-%06d", seq_along(exact_name_key)),
    exact_name_key,
    zipcode,
    city,
    state,
    occupation_key,
    employer_key,
    known_executive,
    verified_employee
  )
}

assert_parity <- function(group, label) {
  expected <- assign_identity_components_reference(group)$identity_component
  observed <- assign_identity_components(group)$identity_component
  if (!identical(observed, expected)) {
    stop(
      sprintf(
        "%s mismatch: expected=%s observed=%s",
        label,
        paste(expected, collapse = ","),
        paste(observed, collapse = ",")
      ),
      call. = FALSE
    )
  }
}

representative_groups <- list(
  independent = make_group(c("ALPHA", "ALPHA")),
  same_zip = make_group(c("ALPHA", "BETA"), c("10001", "10001-1234")),
  same_location = make_group(
    c("ALPHA", "BETA"),
    city = c("NEW YORK", "NEW YORK"),
    state = c("NY", "NY")
  ),
  exact_occupation = make_group(
    c("ALPHA", "ALPHA"),
    occupation_key = c("ENGINEER", "ENGINEER")
  ),
  variant_occupation_employer = make_group(
    c("ALPHA", "BETA"),
    occupation_key = c("ENGINEER", "ENGINEER"),
    employer_key = c("ACME", "ACME")
  ),
  blank_exact_occupation_employer = make_group(
    c("", ""),
    occupation_key = c("ENGINEER", "ENGINEER"),
    employer_key = c("ACME", "ACME")
  ),
  transitive = make_group(
    c("ALPHA", "BETA", "GAMMA"),
    zipcode = c("10001", "10001", ""),
    city = c("", "CHICAGO", "CHICAGO"),
    state = c("", "IL", "IL")
  ),
  executive_override = make_group(
    c("ALPHA", "BETA", "GAMMA"),
    known_executive = c(FALSE, TRUE, FALSE)
  ),
  verified_override = make_group(
    c("ALPHA", "BETA", "GAMMA"),
    verified_employee = c(FALSE, FALSE, TRUE)
  )
)
for (label in names(representative_groups)) {
  assert_parity(representative_groups[[label]], label)
}

set.seed(20260723)
pool <- list(
  exact_name_key = c("", "ALPHA", "ALPHA", "BETA", "GAMMA", "DELTA"),
  zipcode = c("", "10001", "10001-9999", "60601", "94105"),
  city = c("", "NEW YORK", "CHICAGO", "SAN FRANCISCO"),
  state = c("", "NY", "IL", "CA"),
  occupation_key = c("", "ENGINEER", "EXECUTIVE", "COUNSEL", "MANAGER"),
  employer_key = c("", "ACME", "BETA CORP", "GAMMA LLC")
)
random_cases <- 1000L
for (case_index in seq_len(random_cases)) {
  count <- sample.int(50L, 1L)
  group <- make_group(
    sample(pool$exact_name_key, count, replace = TRUE),
    sample(pool$zipcode, count, replace = TRUE),
    sample(pool$city, count, replace = TRUE),
    sample(pool$state, count, replace = TRUE),
    sample(pool$occupation_key, count, replace = TRUE),
    sample(pool$employer_key, count, replace = TRUE)
  )
  if (case_index %% 100L == 0L) {
    group$known_executive[sample.int(count, 1L)] <- TRUE
  }
  if (case_index %% 125L == 0L) {
    group$verified_employee[sample.int(count, 1L)] <- TRUE
  }
  group <- group[sample.int(count)]
  assert_parity(group, sprintf("random case %d", case_index))
}

benchmark_group <- make_group(
  exact_name_key = rep(c("ALPHA", "BETA", ""), length.out = 750L),
  zipcode = rep(c("10001", "10001-9999", ""), length.out = 750L),
  city = rep(c("NEW YORK", "NEW YORK", ""), length.out = 750L),
  state = rep(c("NY", "NY", ""), length.out = 750L),
  occupation_key = rep(c("ENGINEER", "ENGINEER", "COUNSEL"), length.out = 750L),
  employer_key = rep(c("ACME", "ACME", "BETA"), length.out = 750L)
)
reference_seconds <- unname(system.time(
  reference_result <- assign_identity_components_reference(benchmark_group)
)[["elapsed"]])
indexed_seconds <- unname(system.time(
  indexed_result <- assign_identity_components(benchmark_group)
)[["elapsed"]])
stopifnot(identical(
  reference_result$identity_component,
  indexed_result$identity_component
))

speedup <- if (indexed_seconds > 0) {
  reference_seconds / indexed_seconds
} else {
  Inf
}
cat(sprintf(
  paste0(
    "test_identity_components: representative=%d randomized=%d rows=%d ",
    "reference=%.3fs indexed=%.3fs speedup=%.1fx PASS\n"
  ),
  length(representative_groups),
  random_cases,
  nrow(benchmark_group),
  reference_seconds,
  indexed_seconds,
  speedup
))
