# The upstream JS-member budget is checked only between complete URL fallback
# chains. Share one monotonic deadline across the HTTP/Wayback requests instead.
# This adapter is for refresh_press_delta.R's browser-disabled production lane;
# it does not claim to bound native Chrome startup/cleanup with R time limits.
install_press_request_deadlines <- function(
    envir = .GlobalEnv,
    clock = function() unname(proc.time()[["elapsed"]]),
    sleep = Sys.sleep) {
  if (isTRUE(get("HAVE_CHROMOTE", envir = envir, inherits = TRUE)))
    stop("press network deadline adapter requires browser rendering disabled")
  original_member <- get("fetch_member_fulltext", envir = envir, inherits = FALSE)
  if (isTRUE(attr(original_member, "melange_press_request_deadlines")))
    return(invisible(NULL))

  state <- new.env(parent = emptyenv())
  state$deadline <- NULL
  state$expired <- FALSE
  state$requested <- character(0)
  state$attempted <- character(0)
  state$current_url <- NULL
  state$interrupted_url <- NULL
  spent <- new.env(parent = emptyenv())
  cached_complete <- function(frame) {
    if (is.null(frame)) return(logical(0))
    if (exists("press_cached_body_complete", envir = envir, inherits = FALSE)) {
      result <- get("press_cached_body_complete", envir = envir, inherits = FALSE)(frame)
    } else {
      result <- if (!all(c("body", "date_detail") %in% names(frame))) rep(FALSE, nrow(frame)) else
        !is.na(frame$body) & nzchar(frame$body) & !is.na(frame$date_detail)
    }
    if (!is.logical(result) || length(result) != nrow(frame) || anyNA(result))
      stop("press cached-body predicate returned an invalid result")
    result
  }
  # Keep the full previous checkpoint, but let the repository's validated-date
  # predicate decide which rows can skip a fetch or satisfy a JS fallback.
  # Invalid cached dates must not keep a URL permanently out of the retry queue.
  member_bindings <- new.env(parent = environment(original_member))
  member_bindings$.press_cached_body_complete <- cached_complete
  member_changes <- c(done = 0L, ok_r = 0L)
  rewrite_member <- function(expression) {
    if (!is.call(expression)) return(expression)
    if (identical(expression[[1]], as.name("<-")) && is.symbol(expression[[2]])) {
      name <- as.character(expression[[2]])
      if (name == "done") {
        member_changes[[name]] <<- member_changes[[name]] + 1L
        expression[[3]] <- quote(if (is.null(prev)) character(0) else
                                    prev$url[.press_cached_body_complete(prev)])
        return(expression)
      }
      if (name == "ok_r") {
        member_changes[[name]] <<- member_changes[[name]] + 1L
        expression[[3]] <- quote(function(r) if (js)
          !is.null(r) && nrow(r) == 1L && isTRUE(.press_cached_body_complete(r)[[1L]]) else got_body(r))
        return(expression)
      }
    }
    parts <- as.list(expression)
    for (index in seq_along(parts)[-1L]) {
      # Preserve missing subscripts such as frame[keep, ] without evaluating a
      # missing argument while walking the upstream function definition.
      if (identical(unname(parts[index]), unname(alist(value = )))) next
      parts[index] <- list(rewrite_member(parts[[index]]))
    }
    as.call(parts)
  }
  body(original_member) <- rewrite_member(body(original_member))
  if (any(member_changes != 1L))
    stop("upstream cached-body acceptance contract changed; review deadline adapter")
  environment(original_member) <- member_bindings
  active <- function() !is.null(state$deadline)
  remaining <- function() {
    if (!active()) return(Inf)
    value <- max(0, state$deadline - clock())
    if (value < 0.001) {
      if (!state$expired) state$interrupted_url <- state$current_url
      state$expired <- TRUE
      return(0)
    }
    value
  }
  bounded_sleep <- function(seconds) {
    if (!active()) return(sleep(seconds))
    available <- remaining()
    if (available > 0) sleep(min(seconds, available))
    remaining()
    invisible(NULL)
  }

  # Keep the upstream retry counts, headers, response parsing and status rules.
  # Change only its named HTTP/time primitives in private function copies. Fail
  # initialization if a changed upstream implementation no longer has the calls
  # we can bound; never silently fall back to an unbounded implementation.
  adapt <- function(name, required) {
    original <- get(name, envir = envir, inherits = FALSE)
    bindings <- new.env(parent = environment(original))
    original_get <- get("GET", envir = environment(original), inherits = TRUE)
    original_timeout <- get("timeout", envir = environment(original), inherits = TRUE)
    bindings$.press_deadline_sleep <- bounded_sleep
    bindings$.press_deadline_get <- function(u, ...) {
      if (active() && remaining() <= 0) stop("press member network deadline exhausted")
      if (active() && u %in% state$requested)
        state$attempted <- unique(c(state$attempted, u))
      original_get(u, ...)
    }
    bindings$.press_deadline_timeout <- function(seconds) {
      if (!active()) return(original_timeout(seconds))
      # httr stores milliseconds; rounding a sub-millisecond timeout to zero
      # would mean unlimited time to curl. Never issue that request.
      available <- floor(remaining() * 1000) / 1000
      if (available <= 0) stop("press member network deadline exhausted")
      original_timeout(min(seconds, available))
    }
    replacements <- c(GET = ".press_deadline_get", timeout = ".press_deadline_timeout",
                      Sys.sleep = ".press_deadline_sleep")
    seen <- setNames(integer(length(replacements)), names(replacements))
    rewrite <- function(expression) {
      if (!is.call(expression)) return(expression)
      if (is.symbol(expression[[1]])) {
        name <- as.character(expression[[1]])
        if (name %in% names(replacements)) {
          seen[[name]] <<- seen[[name]] + 1L
          expression[[1]] <- as.name(replacements[[name]])
        }
      }
      for (index in seq_along(expression)[-1L])
        expression[index] <- list(rewrite(expression[[index]]))
      expression
    }
    body(original) <- rewrite(body(original))
    if (any(seen[names(required)] != required))
      stop("upstream ", name, " HTTP/time contract changed; review deadline adapter")
    environment(original) <- bindings
    if (name == "fetch_html") {
      original_fetch <- original
      function(u, ...) {
        if (active() && u %in% state$requested) state$current_url <- u
        original_fetch(u, ...)
      }
    } else original
  }
  replacements <- list(
    fetch_html = adapt("fetch_html", c(GET = 1L, timeout = 1L, Sys.sleep = 2L)),
    cdx_get = adapt("cdx_get", c(GET = 1L, timeout = 1L, Sys.sleep = 1L)),
    wayback_snapshot_url = adapt("wayback_snapshot_url", c(GET = 0L, timeout = 0L,
                                                          Sys.sleep = 1L))
  )
  member <- function(bg, urls, force = FALSE, js = FALSE) {
    if (!isTRUE(js)) return(original_member(bg, urls, force = force, js = js))
    if (isTRUE(get("HAVE_CHROMOTE", envir = envir, inherits = TRUE)))
      stop("press network deadline adapter requires browser rendering disabled")
    budget <- get("JS_MEMBER_BUDGET", envir = envir, inherits = TRUE)
    if (length(budget) != 1L || !is.finite(budget) || budget <= 0)
      stop("JS_MEMBER_BUDGET must be a finite positive duration")
    previous_spent <- if (exists(bg, envir = spent, inherits = FALSE)) spent[[bg]] else 0
    available_budget <- max(0, budget - previous_spent)
    started <- clock()
    previous <- as.list(state)
    state$deadline <- min(if (is.null(previous$deadline)) Inf else previous$deadline,
                          started + available_budget)
    state$expired <- FALSE
    state$requested <- unique(urls)
    state$attempted <- character(0)
    state$current_url <- NULL
    state$interrupted_url <- NULL
    on.exit({
      spent[[bg]] <- previous_spent + max(0, clock() - started)
      for (name in names(previous)) state[[name]] <- previous[[name]]
    }, add = TRUE)
    out <- original_member(bg, urls, force = force, js = js)
    completed <- if (is.null(out) || !all(c("url", "body", "date_detail") %in% names(out)))
      character(0) else out$url[cached_complete(out)]
    deferred <- if (state$expired)
      unique(c(setdiff(urls, c(completed, state$attempted)),
               setdiff(state$interrupted_url, completed))) else character(0)
    if (exists("press_member_network_result", envir = envir, inherits = FALSE))
      get("press_member_network_result", envir = envir, inherits = FALSE)(list(
        bioguide = bg, requested_urls = urls, attempted_urls = state$attempted,
        completed_urls = completed, budget_exhausted = state$expired,
        budget_deferred_urls = deferred
      ))
    if (isTRUE(state$expired))
      message("    [network deadline] ", bg, ": request budget exhausted; ",
              "completed bodies saved and unresolved URLs remain retryable.")
    out
  }
  attr(member, "melange_press_request_deadlines") <- TRUE
  original_stage <- get("stage_fulltext", envir = envir, inherits = FALSE)
  stage <- function(...) withCallingHandlers(original_stage(...), message = function(condition) {
    text <- conditionMessage(condition)
    if (grepl(" JS member(s) via chromote ", text, fixed = TRUE)) {
      text <- sub("JS member(s) via chromote",
                  "fallback member(s) via HTTP/Wayback (browser disabled)", text, fixed = TRUE)
      text <- sub("s/member cap)", "s/member network deadline)", text, fixed = TRUE)
      cat(text, file = stderr())
      flush.console()
      invokeRestart("muffleMessage")
    }
  })
  # Install only after every upstream compatibility check has passed.
  for (name in names(replacements)) assign(name, replacements[[name]], envir = envir)
  assign("fetch_member_fulltext", member, envir = envir)
  assign("stage_fulltext", stage, envir = envir)
  invisible(NULL)
}
