# The permission-gated, polite OpenSecrets page fetcher (FEEDS_SPEC 3.1, 3.2) - the ONLY file of the ingest allowed to touch the
# network. The terms of the site bar automated access without express permission, so the fetcher runs only when the registry
# carries the owner's attestation of that permission (the owner states he holds OpenSecrets' explicit permission; the attestation
# is recorded in registry/feeds.json: who, date, basis). It is polite and honest:
#   - a descriptive User-Agent naming the tool and its operator; never spoofed, never rotated
#   - at most one request per min_interval_s (default 4 s, never below 3 s), one connection, no cookies kept, no proxy
#   - robots.txt fetched first and obeyed (Disallow matches are skipped); a non-200 robots response stops the run
#   - a content-addressed cache: each URL is fetched once; later runs are cache hits with no request
#   - only the URLs the registry lists for the industry; the frozen baseline codes are never fetched
#
# HARD LIMIT: no bot-detection or CAPTCHA circumvention of any kind. There is no browser, no script execution, no proxy or
# unblocker and no header spoofing in this module. A challenge (403 "Just a moment", a cf-mitigated header, a captcha body,
# 401/407/429/503) or any other block STOPS the run at once: the stop is written to fetch_state.json, the receipt and the Glass
# Box, and the owner is told to obtain an allowlisted access method from OpenSecrets or to drop the bulk files in the inbox.
# The transport is injectable so the tests never touch the network.

fi_fetch_pkg_versions <- function() list(curl = if (requireNamespace("curl", quietly = TRUE)) utils::packageDescription("curl")$Version else NA_character_)

FI_OS_STOP_MESSAGE <- paste("OpenSecrets blocked or challenged automated access; ask OpenSecrets for an allowlisted access method",
                            "or drop the bulk-data files in the inbox (FEEDS_SPEC section 4).")
FI_OS_CHALLENGE_MARKERS <- c("just a moment", "performing security verification", "/cdn-cgi/challenge-platform", "captcha")

# The one real transport: a single sequential libcurl request, redirects not followed, no cookie engine, no proxy setting.
# -> list(status, headers (lower-case names), body (raw), url)
fi_curl_transport <- function(url, user_agent, timeout_s = 60) {
  if (!requireNamespace("curl", quietly = TRUE)) stop("fi_curl_transport: package curl is unavailable", call. = FALSE)
  h <- curl::new_handle(useragent = user_agent, followlocation = FALSE, maxredirs = 0L, timeout = as.integer(timeout_s), connecttimeout = 30L,
                        accept_encoding = "")
  curl::handle_setheaders(h, Accept = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5")
  res <- curl::curl_fetch_memory(url, handle = h)
  hdr <- curl::parse_headers_list(res$headers)
  names(hdr) <- tolower(names(hdr))
  list(status = as.integer(res$status_code), headers = hdr, body = res$content, url = url)
}

# ---- robots.txt ---------------------------------------------------------------------------------------------------------

# Groups of a robots.txt: list of list(agents, rules = data.frame(type, path)).
fi_robots_parse <- function(text) {
  lines <- trimws(sub("#.*$", "", strsplit(gsub("\r", "", text, fixed = TRUE), "\n", fixed = TRUE)[[1]]))
  groups <- list(); cur <- NULL; last_was_agent <- FALSE
  for (ln in lines[nzchar(lines)]) {
    m <- regmatches(ln, regexec("^([A-Za-z-]+)\\s*:\\s*(.*)$", ln))[[1]]
    if (length(m) != 3L) next
    key <- tolower(m[2]); val <- trimws(m[3])
    if (key == "user-agent") {
      if (is.null(cur) || !last_was_agent) { if (!is.null(cur)) groups[[length(groups) + 1L]] <- cur; cur <- list(agents = character(), rules = data.frame(type = character(), path = character(), stringsAsFactors = FALSE)) }
      cur$agents <- c(cur$agents, tolower(val)); last_was_agent <- TRUE
    } else if (key %in% c("allow", "disallow") && !is.null(cur)) {
      cur$rules[nrow(cur$rules) + 1L, ] <- list(key, val); last_was_agent <- FALSE
    } else {
      last_was_agent <- FALSE
    }
  }
  if (!is.null(cur)) groups[[length(groups) + 1L]] <- cur
  groups
}

.fi_robots_regex <- function(path) {
  anchored <- grepl("\\$$", path)
  core <- sub("\\$$", "", path)
  pieces <- strsplit(core, "*", fixed = TRUE)[[1]]
  esc <- vapply(pieces, function(p) gsub("([.+?^${}()|\\[\\]\\\\])", "\\\\\\1", p, perl = TRUE), "")
  rx <- paste(esc, collapse = ".*")
  if (grepl("\\*$", core)) rx <- paste0(rx, ".*")
  paste0("^", rx, if (anchored) "$" else "")
}

# Is `path` (with its query string) allowed for the product token `agent`? The group naming the token wins over "*"; the longest
# matching rule wins, Allow beating Disallow on a tie; no matching rule means allowed.
fi_robots_allowed <- function(groups, path, agent = "mentat-giving-forecast") {
  agent <- tolower(agent)
  named <- Filter(function(g) any(vapply(g$agents, function(a) a != "*" && startsWith(agent, a), NA)), groups)
  star <- Filter(function(g) "*" %in% g$agents, groups)
  use <- if (length(named)) named else star
  if (!length(use)) return(TRUE)
  rules <- do.call(rbind, lapply(use, function(g) g$rules))
  rules <- rules[nzchar(rules$path), , drop = FALSE]
  if (!nrow(rules)) return(TRUE)
  hit <- vapply(rules$path, function(p) grepl(.fi_robots_regex(p), path, perl = TRUE), NA)
  if (!any(hit)) return(TRUE)
  h <- rules[hit, , drop = FALSE]
  len <- nchar(h$path)
  top <- h[len == max(len), , drop = FALSE]
  any(top$type == "allow")
}

# ---- state, cache, gate ---------------------------------------------------------------------------------------------------

fi_fetch_state_path <- function(frt = fi_frt_dir()) file.path(frt, "fetch_state.json")
fi_fetch_state_read <- function(frt = fi_frt_dir()) {
  p <- fi_fetch_state_path(frt)
  if (!file.exists(p)) return(list(stopped = FALSE))
  tryCatch(jsonlite::fromJSON(p, simplifyVector = FALSE), error = function(e) list(stopped = TRUE, reason = "fetch_state.json unreadable"))
}
fi_fetch_state_stop <- function(reason, url, evidence = NULL, frt = fi_frt_dir()) {
  rf_write_json_atomic(list(stopped = TRUE, reason = reason, url = url, utc = rf_now_utc(), evidence = evidence,
                            cleared_by = "only the owner clears this flag (set stopped to false) after obtaining an allowlisted access method"),
                       fi_fetch_state_path(frt))
}

fi_os_cache_dir <- function(url, frt = fi_frt_dir()) file.path(frt, "os_cache", fi_sha256_raw(url))

# The URLs the fetcher may request for an industry: the pages of FEEDS_SPEC 2.2 - one totals page, then House / Senate / presidential
# recipients per required cycle - for the codes of the registry entry. -> data.frame(url, code, cycle, kind, role)
#   inventory  the frozen page inventory (fi_os_frozen_inventory): pages already captured are left out of the plan (and kept in attr "present"),
#              so only the missing pages are ever requested
fi_os_fetch_plan <- function(feeds, industry_id, cycles = NULL, inventory = NULL) {
  ind <- feeds$industries[[industry_id]]
  if (is.null(ind)) stop("fi_os_fetch_plan: industry not in the feeds registry: ", industry_id, call. = FALSE)
  code <- ind$opensecrets$codes[[1]]
  cyc <- cycles %||% unlist(ind$opensecrets$cycles_required)
  anchor <- ind$opensecrets$totals_cycle_anchor %||% max(cyc)
  rows <- list(data.frame(url = fi_os_url(code, anchor, "totals"), code = code, cycle = as.integer(anchor), kind = "totals",
                          role = sprintf("os_totals:%s", industry_id), stringsAsFactors = FALSE))
  for (cy in cyc) for (k in c("candidates_H", "candidates_S", "presidential")) {
    role <- if (k == "presidential") sprintf("os_presidential:%s:%d", industry_id, cy) else sprintf("os_recipients:%s:%d:%s", industry_id, cy, substr(k, 12, 12))
    rows[[length(rows) + 1L]] <- data.frame(url = fi_os_url(code, cy, k), code = code, cycle = as.integer(cy), kind = k, role = role, stringsAsFactors = FALSE)
  }
  full <- do.call(rbind, rows)
  have <- fi_os_in_inventory(inventory, full$code, full$cycle, full$kind)
  out <- full[!have, , drop = FALSE]
  rownames(out) <- NULL
  attr(out, "present") <- full[have, , drop = FALSE]
  attr(out, "expected") <- full
  out
}

fi_os_user_agent <- function(cfg, contact = NULL) {
  contact <- contact %||% { v <- trimws(Sys.getenv("MENTAT_OS_CONTACT", unset = "")); if (nzchar(v)) v else (cfg$user_agent_contact %||% "") }
  if (!nzchar(contact)) return(NA_character_)
  sprintf("Mentat-Giving-Forecast/%s (research data fetcher; %s)", sub("^ingest-", "", FI$VERSION), contact)
}

# The gate. Every condition must hold before any request is made (zero transport calls otherwise).
# -> list(ok, reason, user_agent)
fi_os_fetch_gate <- function(cfg, plan, frozen_codes, frt = fi_frt_dir(), contact = NULL, inventory = NULL) {
  stop_with <- function(why) list(ok = FALSE, reason = why, user_agent = NA_character_)
  perm <- cfg$permission
  need <- c("kind", "who", "date", "basis")
  if (is.null(perm) || !all(vapply(need, function(k) is.character(perm[[k]]) && length(perm[[k]]) == 1L && nzchar(trimws(perm[[k]])), NA)))
    return(stop_with("no permission attestation in the registry (opensecrets_fetch.permission needs kind, who, date, basis)"))
  ua <- fi_os_user_agent(cfg, contact)
  if (is.na(ua)) return(stop_with("no contact for the User-Agent (set MENTAT_OS_CONTACT or opensecrets_fetch.user_agent_contact)"))
  st <- fi_fetch_state_read(frt)
  if (isTRUE(st$stopped)) return(stop_with(sprintf("fetch_state.json says stopped (%s at %s); only the owner clears it", st$reason %||% "no reason", st$url %||% "?")))
  patterns <- unlist(cfg$url_patterns)
  hosts <- unlist(cfg$allowed_hosts)
  for (u in plan$url) {
    host <- sub("^https?://([^/]+).*$", "\\1", u)
    if (!(host %in% hosts)) return(stop_with(paste("URL host not allowed:", u)))
    if (!any(vapply(patterns, function(p) grepl(p, u, perl = TRUE), NA))) return(stop_with(paste("URL does not match an allowed pattern:", u)))
    tg <- fi_os_target_from_url(u)
    if (!is.na(tg$code) && tg$code %in% frozen_codes) return(stop_with(paste("code", tg$code, "belongs to the frozen baseline and is never re-fetched:", u)))
    if (!is.na(tg$code) && !is.na(tg$cycle) && !is.na(tg$kind) && fi_os_in_inventory(inventory, tg$code, tg$cycle, tg$kind))
      return(stop_with(paste("this page is in the frozen capture inventory and is never re-fetched:", u)))
  }
  list(ok = TRUE, reason = "permission attested, contact set, not stopped, every URL allowed", user_agent = ua)
}

# A response that means "stop": NULL when the page is acceptable HTML, otherwise list(kind = "block"|"error"|"redirect", reason).
fi_os_classify_response <- function(resp, redirects_ok = FALSE) {
  st <- resp$status
  h <- resp$headers
  body_txt <- if (length(resp$body)) tryCatch(tolower(iconv(rawToChar(resp$body[resp$body != as.raw(0L)]), "UTF-8", "UTF-8", sub = " ")), error = function(e) "") else ""
  if (!is.null(h[["cf-mitigated"]])) return(list(kind = "block", reason = "cf_mitigated_header"))
  if (st %in% c(401L, 403L, 407L, 429L, 503L)) return(list(kind = "block", reason = sprintf("http_%d", st)))
  if (any(vapply(FI_OS_CHALLENGE_MARKERS, function(m) grepl(m, body_txt, fixed = TRUE), NA))) return(list(kind = "block", reason = "challenge_page_body"))
  if (st %in% c(301L, 302L, 303L, 307L, 308L)) return(list(kind = "redirect", reason = sprintf("http_%d_redirect", st)))
  if (st %in% c(404L, 410L)) return(list(kind = "error", reason = sprintf("http_%d_page_not_found", st)))
  if (st >= 500L) return(list(kind = "retry", reason = sprintf("http_%d", st)))
  if (st != 200L) return(list(kind = "error", reason = sprintf("http_%d_unexpected", st)))
  ct <- tolower(h[["content-type"]] %||% "")
  if (!grepl("text/html", ct, fixed = TRUE)) return(list(kind = "block", reason = paste0("non_html_content_type:", ct)))
  ti <- regmatches(body_txt, regexec("<title[^>]*>([^<]*)</title>", body_txt))[[1]]
  if (length(ti) != 2L || !grepl("opensecrets", ti[2], fixed = TRUE)) return(list(kind = "block", reason = "title_without_OpenSecrets"))
  NULL
}

.fi_header_text <- function(h) paste(paste0(names(h), ": ", vapply(h, function(v) paste(unlist(v), collapse = ", "), "")), collapse = "\n")

# Headers kept as block evidence, minus the ones that carry the owner's own address or a session token (the site echoes the client's
# IP address, and issues a session cookie; neither belongs in a receipt that may be pasted into a report).
FI_REDACTED_HEADERS <- c("set-cookie", "cookie", "cf-connecting-ip", "cf-connecting-ipv6", "x-forwarded-for", "x-real-ip", "true-client-ip", "x-request-id")
fi_redact_headers <- function(h) { h <- h %||% list(); h[setdiff(names(h), FI_REDACTED_HEADERS)] }

# ---- the run ------------------------------------------------------------------------------------------------------------

# Fetch the planned pages politely.
#   plan         data.frame(url, code, cycle, kind, role) in request order
#   cfg          registry opensecrets_fetch block
#   transport    function(url, user_agent, timeout_s) -> list(status, headers, body, url); the real one is fi_curl_transport
#   clock, sleep injectable time (seconds)
#   emit         function(kind, status, stage, summary, detail, metadata) of a Glass Box run, or NULL
#   on_page      function(page) called after each accepted page; returning list(stop = TRUE, reason = ...) ends the run
# -> list(pages (one row per planned URL), stopped (NULL or list(kind, reason, url, evidence)), requests, network (receipt entries), html (list by url))
fi_os_fetch_run <- function(plan, cfg, frozen_codes, frt = fi_frt_dir(), transport = fi_curl_transport, clock = function() as.numeric(Sys.time()),
                            sleep = Sys.sleep, emit = NULL, contact = NULL, refresh = FALSE, on_page = NULL, inventory = NULL) {
  say <- function(kind, status, stage, summary, detail = "", metadata = NULL) if (is.function(emit)) emit(kind, status, stage, summary, detail, metadata)
  gate <- fi_os_fetch_gate(cfg, plan, frozen_codes, frt, contact, inventory)
  say("feeds.fetch.gate", if (gate$ok) "succeeded" else "failed", "fetch-gate", paste("fetch gate:", if (gate$ok) "open" else "closed"), gate$reason,
      list(decision = if (gate$ok) "open" else "stop", reason = gate$reason))
  pages <- data.frame(url = plan$url, role = plan$role, status = "not_attempted", http = NA_integer_, bytes = NA_real_, sha256 = NA_character_,
                      cache_dir = NA_character_, detail = NA_character_, stringsAsFactors = FALSE)
  res <- list(pages = pages, stopped = NULL, requests = 0L, network = list(), html = list(), gate = gate)
  if (!gate$ok) { res$stopped <- list(kind = "gate", reason = gate$reason, url = NA_character_); return(res) }
  ua <- gate$user_agent
  interval <- max(as.numeric(cfg$min_interval_s %||% 5), as.numeric(cfg$min_interval_floor_s %||% 3))
  max_req <- as.integer(cfg$max_requests_per_run %||% 25L)
  timeout <- as.numeric(cfg$request_timeout_s %||% 60)
  st <- new.env(parent = emptyenv()); st$last_end <- NULL; st$n <- 0L
  record <- function(url, http, bytes, cache_hit, reason = NA_character_) {
    e <- list(url = url, utc = rf_now_utc(), http = http, bytes = bytes, cache_hit = cache_hit, stop_reason = reason)
    res$network[[length(res$network) + 1L]] <<- e
    say("feeds.fetch.request", "succeeded", "fetch", sprintf("%s %s", if (cache_hit) "cache hit" else paste("HTTP", http), url), "", list(url = url, http = http, bytes = bytes, cache_hit = cache_hit))
  }
  halt <- function(kind, reason, url, resp = NULL) {
    ev <- if (!is.null(resp)) list(http = resp$status, headers = fi_redact_headers(resp$headers), body_excerpt = substr(tryCatch(iconv(rawToChar(resp$body[resp$body != as.raw(0L)][seq_len(min(1500L, sum(resp$body != as.raw(0L))))]), "UTF-8", "UTF-8", sub = " "), error = function(e) ""), 1, 1500)) else NULL
    if (kind == "block") fi_fetch_state_stop(reason, url, ev, frt)
    record(url, if (!is.null(resp)) resp$status else NA_integer_, if (!is.null(resp)) length(resp$body) else NA_integer_, FALSE, reason)
    say("feeds.fetch.stop", "failed", "fetch", paste("fetch stopped:", reason), url, list(reason = reason, url = url))
    res$stopped <<- list(kind = kind, reason = reason, url = url, evidence = ev)
  }
  request <- function(url) {
    # one polite request: rate limit, then the transport
    if (st$n >= max_req) return(list(stop = "max_requests_per_run_reached"))
    if (!is.null(st$last_end)) { wait <- interval - (clock() - st$last_end); if (wait > 0) sleep(wait) }
    st$n <- st$n + 1L
    r <- transport(url, ua, timeout)
    st$last_end <- clock()
    res$requests <<- st$n
    r$headers <- r$headers %||% list()
    names(r$headers) <- tolower(names(r$headers))
    r
  }
  # robots.txt first (cached for max-age)
  rdir <- file.path(frt, "os_cache", "robots")
  robots_text <- NULL
  mpath <- file.path(rdir, "meta.json")
  if (file.exists(mpath) && file.exists(file.path(rdir, "robots.txt"))) {
    m <- jsonlite::fromJSON(mpath, simplifyVector = FALSE)
    age <- as.numeric(difftime(Sys.time(), as.POSIXct(m$fetched_utc, format = "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"), units = "secs"))
    if (!is.na(age) && age < (m$max_age %||% 86400)) { robots_text <- paste(readLines(file.path(rdir, "robots.txt"), warn = FALSE, encoding = "UTF-8"), collapse = "\n"); record(paste0(FI_OS_BASE, "/robots.txt"), 200L, file.size(file.path(rdir, "robots.txt")), TRUE) }
  }
  if (is.null(robots_text)) {
    ru <- paste0(FI_OS_BASE, "/robots.txt")
    r <- request(ru)
    if (!is.null(r$stop)) { halt("error", r$stop, ru); return(res) }
    cls <- if (r$status == 200L && grepl("text/plain", tolower(r$headers[["content-type"]] %||% ""), fixed = TRUE)) NULL else
      (if (!is.null(r$headers[["cf-mitigated"]]) || r$status %in% c(401L, 403L, 407L, 429L, 503L)) list(kind = "block", reason = paste0("robots_http_", r$status)) else list(kind = "error", reason = paste0("robots_not_200_text:", r$status)))
    if (!is.null(cls)) { halt(cls$kind, cls$reason, ru, r); return(res) }
    robots_text <- rawToChar(r$body); Encoding(robots_text) <- "UTF-8"
    dir.create(rdir, recursive = TRUE, showWarnings = FALSE)
    rf_write_text_atomic(robots_text, file.path(rdir, "robots.txt"))
    maxage <- suppressWarnings(as.numeric(sub(".*max-age=([0-9]+).*", "\\1", r$headers[["cache-control"]] %||% "")))
    rf_write_json_atomic(list(fetched_utc = rf_now_utc(), max_age = if (is.na(maxage)) 86400 else maxage, http = r$status, sha256 = fi_sha256_raw(r$body)), mpath)
    record(ru, r$status, length(r$body), FALSE)
  }
  groups <- fi_robots_parse(robots_text)
  # optional terms check
  if (!is.null(cfg$terms_sha256_reviewed) && nzchar(cfg$terms_sha256_reviewed)) {
    tu <- paste0(FI_OS_BASE, "/about/terms-and-conditions")
    if (!fi_robots_allowed(groups, "/about/terms-and-conditions")) { halt("error", "terms_page_disallowed_by_robots", tu); return(res) }
    r <- request(tu)
    if (!is.null(r$stop)) { halt("error", r$stop, tu); return(res) }
    cls <- fi_os_classify_response(r)
    if (!is.null(cls)) { halt(cls$kind, cls$reason, tu, r); return(res) }
    if (!identical(fi_sha256_raw(r$body), cfg$terms_sha256_reviewed)) { halt("error", "terms_changed_since_review", tu, r); return(res) }
    record(tu, r$status, length(r$body), FALSE)
  }
  for (i in seq_len(nrow(plan))) {
    url <- plan$url[i]
    path <- sub("^https?://[^/]+", "", url)
    if (!fi_robots_allowed(groups, path)) {
      res$pages$status[i] <- "skipped_robots"; res$pages$detail[i] <- "robots.txt disallows this path"
      say("feeds.fetch.request", "failed", "fetch", paste("skipped (robots.txt):", url), "", list(url = url, http = NA, bytes = NA, cache_hit = FALSE))
      next
    }
    cdir <- fi_os_cache_dir(url, frt)
    body <- NULL
    final_url <- url
    if (!refresh && file.exists(file.path(cdir, "body.html")) && file.exists(file.path(cdir, "meta.json"))) {
      body <- fi_read_raw(file.path(cdir, "body.html"))
      meta <- jsonlite::fromJSON(file.path(cdir, "meta.json"), simplifyVector = FALSE)
      res$pages$status[i] <- "cache_hit"; res$pages$http[i] <- as.integer(meta$http); res$pages$bytes[i] <- length(body)
      res$pages$sha256[i] <- fi_sha256_raw(body); res$pages$cache_dir[i] <- cdir
      record(url, as.integer(meta$http), length(body), TRUE)
      retrieved <- meta$retrieved_utc
      final_url <- meta$final_url %||% url
    } else {
      cur <- url; hops <- 0L; r <- NULL; retried <- FALSE
      repeat {
        r <- request(cur)
        if (!is.null(r$stop)) { halt("error", r$stop, cur); return(res) }
        cls <- fi_os_classify_response(r)
        if (is.null(cls)) break
        if (cls$kind == "retry" && !retried) {
          # a 5xx other than 503: exactly one retry, after Retry-After or 60 s
          retried <- TRUE
          ra <- suppressWarnings(as.numeric(r$headers[["retry-after"]] %||% NA))
          sleep(if (is.na(ra)) 60 else min(ra, 300))
          next
        }
        if (cls$kind == "retry") { halt("block", paste0(cls$reason, "_after_retry"), cur, r); return(res) }
        if (cls$kind == "redirect") {
          loc <- r$headers[["location"]] %||% ""
          target <- if (grepl("^https?://", loc)) loc else if (startsWith(loc, "//")) "" else if (startsWith(loc, "/")) paste0(FI_OS_BASE, loc) else ""   # "//host/x" names another host
          if (!nzchar(target) || !startsWith(target, paste0(FI_OS_BASE, "/"))) {
            # The same organisation's other host (for example the apex domain) is not a block: the site answered, and its answer is
            # where it sends a page that does not exist. The run still stops (the host is not on the allow list) but sets no sticky flag.
            thost <- tolower(sub("^https?://([^/:]+).*$", "\\1", target))
            if (nzchar(target) && (thost == "opensecrets.org" || endsWith(thost, ".opensecrets.org"))) {
              halt("error", paste0("redirect_to_other_opensecrets_host:", target), cur, r); return(res)
            }
            halt("block", "redirect_off_site", cur, r); return(res)
          }
          if (grepl("login|sign[_-]?in|/users/|/account", sub("^https?://[^/]+", "", target), ignore.case = TRUE)) { halt("block", "redirect_to_login", cur, r); return(res) }
          hops <- hops + 1L
          if (hops > 3L) { halt("error", "too_many_redirects", cur, r); return(res) }
          if (!fi_robots_allowed(groups, sub("^https?://[^/]+", "", target))) { halt("error", "redirect_target_disallowed_by_robots", cur, r); return(res) }
          record(cur, r$status, length(r$body), FALSE)
          cur <- target
          next
        }
        halt(cls$kind, cls$reason, cur, r); return(res)
      }
      body <- r$body
      retrieved <- rf_now_utc()
      dir.create(cdir, recursive = TRUE, showWarnings = FALSE)
      con <- file(file.path(cdir, "body.html"), open = "wb"); writeBin(body, con); close(con)
      rf_write_text_atomic(.fi_header_text(r$headers), file.path(cdir, "headers.txt"))
      rf_write_json_atomic(list(url = url, final_url = cur, http = r$status, retrieved_utc = retrieved, bytes = length(body), sha256 = fi_sha256_raw(body)), file.path(cdir, "meta.json"))
      res$pages$status[i] <- "fetched"; res$pages$http[i] <- r$status; res$pages$bytes[i] <- length(body); res$pages$sha256[i] <- fi_sha256_raw(body)
      res$pages$cache_dir[i] <- cdir
      record(url, r$status, length(body), FALSE)
      final_url <- cur
    }
    html <- rawToChar(body); Encoding(html) <- "UTF-8"
    res$html[[url]] <- html
    if (is.function(on_page)) {
      act <- on_page(list(index = i, url = url, final_url = final_url, row = plan[i, ], html = html, sha256 = res$pages$sha256[i], retrieved_utc = retrieved,
                          http = res$pages$http[i], status = res$pages$status[i], cache_dir = cdir))
      if (is.list(act) && isTRUE(act$stop)) { res$stopped <- list(kind = "verification", reason = act$reason %||% "stopped by the page handler", url = url, evidence = NULL); return(res) }
    }
  }
  res
}
