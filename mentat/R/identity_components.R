# Flag (company, zip5) addresses that behave like an employer's business address
# rather than somewhere people live. The discriminating signature is a large
# shared address whose contributors nearly all report ONE boilerplate occupation
# - a real residential cluster shows varied job titles. Measured on the credit
# union book this separates cleanly with no overlap: SchoolsFirst's Tustin HQ
# scores 96% on a single occupation ("EXECUTIVE", 67 different people), while
# every residential cluster (Navy Federal's Pensacola zips, SECU, Suncoast)
# scores 8-76%. Thresholds sit in the gap.
identify_business_addresses <- function(
  rows,
  min_people = 10L,
  min_occupation_share = 0.80
) {
  require_package("data.table")
  # NB: do not name this `key` - data.table resolves a same-named local variable
  # ahead of the column in `by=`, which silently corrupts the grouping.
  addr_key <- paste(rows$company, substr(rows$zipcode, 1L, 5L), sep = "\r")
  eligible <- nzchar(trim_text(rows$zipcode)) & nzchar(trim_text(rows$occupation_key))
  if (!any(eligible)) {
    return(rep(FALSE, nrow(rows)))
  }
  stats <- data.table::data.table(
    addr = addr_key[eligible],
    person = rows$base_key[eligible],
    occupation = rows$occupation_key[eligible]
  )
  summary <- stats[, {
    counts <- sort(table(occupation), decreasing = TRUE)
    top <- names(counts)[[1L]]
    list(
      people = data.table::uniqueN(person),
      top_share = as.numeric(counts[[1L]]) / .N,
      top_people = data.table::uniqueN(person[occupation == top])
    )
  }, by = "addr"]
  flagged <- summary[
    people >= min_people &
      top_share >= min_occupation_share &
      top_people >= min_people,
    addr
  ]
  addr_key %in% flagged
}

assign_identity_components <- function(group) {
  group <- data.table::copy(group)
  count <- nrow(group)
  if (!count) {
    group[, identity_component := integer()]
    return(group)
  }

  parent <- seq_len(count)
  root <- function(index) {
    while (parent[index] != index) {
      parent[index] <<- parent[parent[index]]
      index <- parent[index]
    }
    index
  }
  unite <- function(left, right) {
    left_root <- root(left)
    right_root <- root(right)
    if (left_root != right_root) {
      lower <- min(left_root, right_root)
      upper <- max(left_root, right_root)
      parent[upper] <<- lower
    }
  }
  unite_key_groups <- function(keys) {
    valid_indices <- which(nzchar(keys))
    if (length(valid_indices) < 2L) {
      return(invisible(NULL))
    }
    for (indices in split(valid_indices, keys[valid_indices])) {
      if (length(indices) < 2L) {
        next
      }
      anchor <- min(indices)
      for (index in indices[indices != anchor]) {
        unite(anchor, index)
      }
    }
    invisible(NULL)
  }

  if (any(group$known_executive) || any(group$verified_employee)) {
    if (count > 1L) {
      for (index in 2L:count) {
        unite(1L, index)
      }
    }
  } else if (count > 1L) {
    # Filings that carry the EMPLOYER's business address instead of a personal
    # one used to mint a phantom second identity: the same person appeared once
    # at home and once at company HQ. A business address is not personal
    # evidence, so it must not act as identity-discriminating location data.
    # Detected upstream (see identify_business_addresses) by the signature that
    # separates it cleanly from a residential cluster - many contributors at one
    # address nearly all sharing a single boilerplate occupation.
    business <- if (!is.null(group$business_address)) {
      !is.na(group$business_address) & group$business_address
    } else {
      rep(FALSE, count)
    }
    # Deliberately NOT suppressing the business address as grouping evidence.
    # Blanking it removes edges, and removing edges can only ever split a person
    # into more pieces - it did exactly that in constructed cases where a work
    # row was the bridge between two home addresses. The work rows uniting with
    # each other via the shared HQ zip is harmless, because this function is
    # already scoped to one (company, canonical name); the fix needed is only an
    # ADDITIONAL edge joining that work cluster to the person's home cluster,
    # applied below. Purely additive, so it cannot fragment.
    zip5 <- ifelse(
      nzchar(group$zipcode),
      substr(group$zipcode, 1L, 5L),
      ""
    )
    location <- ifelse(
      nzchar(group$city) & nzchar(group$state),
      paste(group$city, group$state, sep = "|"),
      ""
    )
    exact_occupation <- ifelse(
      nzchar(group$exact_name_key) & nzchar(group$occupation_key),
      paste(group$exact_name_key, group$occupation_key, sep = "|"),
      ""
    )
    occupation_employer <- ifelse(
      nzchar(group$occupation_key) & nzchar(group$employer_key),
      paste(group$occupation_key, group$employer_key, sep = "|"),
      ""
    )

    # Each index represents one disjunct in the original pairwise predicate.
    # For occupation+employer, same-exact-name pairs are harmless extra edges:
    # exact-name+occupation already connects them. Uniting the complete key
    # group therefore preserves the original connected components, including
    # the original rule that two blank exact-name keys may still merge.
    unite_key_groups(zip5)
    unite_key_groups(location)
    unite_key_groups(exact_occupation)
    unite_key_groups(occupation_employer)

    # Business-address rows carry no personal location evidence of their own, so
    # after the real evidence has been applied they attach to the person this
    # group is already scoped to (same company, same canonical name). Only when
    # the remaining rows form exactly ONE person is that attachment unambiguous;
    # with two or more distinct people we cannot tell whose work address it is,
    # so the row is left on its own rather than guessed.
    # Attaching a work-address row to the person still needs EVIDENCE. Same
    # company + same canonical name alone is too weak - every other edge in this
    # function requires a shared attribute, and merging on the name alone joined
    # "HART, MATTHEW" (Racine WI, EXECUTIVE, CNH Industrial) to "HART, MATTHEW
    # MR." (Washington DC, government-affairs manager) with nothing in common.
    # So require one corroborating signal: same state, same employer, or same
    # occupation. Without one the row keeps its own component, which is the
    # conservative behaviour this register already prefers.
    if (any(business) && !all(business)) {
      personal <- which(!business)
      personal_roots <- unique(vapply(personal, root, integer(1L)))
      if (length(personal_roots) == 1L) {
        anchor <- personal[[1L]]
        corroborates <- function(index) {
          same <- function(a, b) nzchar(a) && nzchar(b) && identical(a, b)
          any(vapply(personal, function(p) {
            same(group$state[[index]], group$state[[p]]) ||
              same(group$employer_key[[index]], group$employer_key[[p]]) ||
              same(group$occupation_key[[index]], group$occupation_key[[p]])
          }, logical(1L)))
        }
        for (index in which(business)) {
          if (corroborates(index)) {
            unite(anchor, index)
          }
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
