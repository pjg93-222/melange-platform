# Official House member/press routes verified with HTTP 200 on 2026-09-13.
# The Clerk binds identity to bioguide; each member's House homepage links the
# press index below. These supplement missing roster fields, never add members.
# https://clerk.house.gov/Members/B001328
# https://clerk.house.gov/Members/G000607
# https://clerk.house.gov/Members/W000832
# https://clerk.house.gov/Members/H001085
# Houlahan's old /news/documentquery.aspx redirects to /press/documentquery.aspx.
PRESS_MEMBER_OVERRIDES <- data.frame(
  bioguide = c("B001328", "G000607", "W000832", "H001085"),
  member = c("Everton Blair Jr.", "James Gallagher", "Aisha Wahab", "Chrissy Houlahan"),
  url = c("https://blair.house.gov", "https://gallagher.house.gov",
          "https://wahab.house.gov", "https://houlahan.house.gov"),
  press_url = c("https://blair.house.gov/media/press-releases",
                "https://gallagher.house.gov/media/press-releases",
                "https://wahab.house.gov/media/press-releases",
                "https://houlahan.house.gov/press/documentquery.aspx?DocumentTypeID=27"),
  stringsAsFactors = FALSE
)

apply_press_member_overrides <- function(members) {
  if (!all(c("bioguide", "member", "url") %in% names(members)))
    stop("press roster is missing required identity or URL columns")
  index <- match(members$bioguide, PRESS_MEMBER_OVERRIDES$bioguide)
  for (field in c("member", "url")) {
    missing <- !is.na(index) & (is.na(members[[field]]) | !nzchar(trimws(members[[field]])))
    members[[field]][missing] <- PRESS_MEMBER_OVERRIDES[[field]][index[missing]]
  }
  members
}

install_press_member_overrides <- function(envir = .GlobalEnv) {
  manual <- get("MANUAL_PRESS_URLS", envir = envir, inherits = FALSE)
  manual[PRESS_MEMBER_OVERRIDES$bioguide] <- PRESS_MEMBER_OVERRIDES$press_url
  assign("MANUAL_PRESS_URLS", manual, envir = envir)
  previous <- get("stage_roster", envir = envir, inherits = FALSE)
  if (!isTRUE(attr(previous, "melange_press_member_overrides"))) {
    repaired <- local({
      original <- previous
      function(force = FALSE) apply_press_member_overrides(original(force = force))
    })
    attr(repaired, "melange_press_member_overrides") <- TRUE
    assign("stage_roster", repaired, envir = envir)
  }
  invisible(NULL)
}
